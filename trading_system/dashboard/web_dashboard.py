"""
Web dashboard (agent.md §18).

Flask app on localhost:5000. Dark theme. No build step.
Reads paper_trades.csv and paper_summary.json.
Auto-updates via JS fetch polling.
"""

from __future__ import annotations

import csv
import json
import logging
import os
from typing import Any

from trading_system.config import settings

logger = logging.getLogger(__name__)

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>RegimeTrader — Paper Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
:root {
  --bg: #0a0a0f; --surface: #12121a; --border: #2a2a40;
  --accent: #2E4BCC; --profit: #00ff9d; --loss: #ff3d5a;
  --warn: #ffb800; --muted: #6b6b8a; --text: #e0e0f0;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: 'SF Mono', 'Consolas', monospace; font-size: 14px; }
.header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 12px 24px; display: flex; align-items: center; gap: 24px; }
.header h1 { font-size: 18px; color: var(--accent); }
.badge { padding: 2px 10px; border-radius: 4px; font-size: 12px; font-weight: 600; }
.badge.calm { background: #00ff9d22; color: var(--profit); }
.badge.normal { background: #ffb80022; color: var(--warn); }
.badge.elevated { background: #ffb80044; color: var(--warn); }
.badge.danger { background: #ff3d5a22; color: var(--loss); }
.kpi-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; padding: 16px 24px; }
.kpi { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; text-align: center; }
.kpi .value { font-size: 24px; font-weight: 700; }
.kpi .label { color: var(--muted); font-size: 12px; margin-top: 4px; }
.profit { color: var(--profit); }
.loss { color: var(--loss); }
.section { padding: 0 24px 16px; }
.section h2 { font-size: 14px; color: var(--muted); margin-bottom: 8px; text-transform: uppercase; letter-spacing: 1px; }
table { width: 100%; border-collapse: collapse; background: var(--surface); border-radius: 8px; overflow: hidden; }
th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border); font-size: 13px; }
th { color: var(--muted); font-weight: 600; }
.chart-container { background: var(--surface); border-radius: 8px; padding: 16px; border: 1px solid var(--border); }
#signals { background: var(--surface); border-radius: 8px; padding: 12px; border: 1px solid var(--border); white-space: pre-wrap; font-size: 12px; max-height: 200px; overflow-y: auto; color: var(--muted); }
.golive { background: var(--surface); border-radius: 8px; padding: 16px; border: 1px solid var(--border); }
.golive .check { display: flex; justify-content: space-between; padding: 4px 0; }
.golive .pass { color: var(--profit); }
.golive .fail { color: var(--loss); }
</style>
</head>
<body>
<div class="header">
  <h1>RegimeTrader</h1>
  <span id="mode" class="badge calm">PAPER</span>
  <span id="regime-badge" class="badge calm">—</span>
  <span id="clock" style="margin-left:auto;color:var(--muted);">—</span>
</div>
<div class="kpi-row">
  <div class="kpi"><div class="value" id="total-pnl">—</div><div class="label">Total P&L</div></div>
  <div class="kpi"><div class="value" id="win-rate">—</div><div class="label">Win Rate</div></div>
  <div class="kpi"><div class="value" id="total-trades">—</div><div class="label">Trades Today</div></div>
  <div class="kpi"><div class="value" id="daily-loss">—</div><div class="label">Daily Loss Used</div></div>
</div>
<div class="section"><h2>Strategy Breakdown</h2>
  <table id="strat-table"><thead><tr><th>Strategy</th><th>Trades</th><th>P&L</th><th>Win%</th></tr></thead><tbody></tbody></table>
</div>
<div class="section"><h2>Equity Curve</h2>
  <div class="chart-container"><canvas id="equity-chart" height="120"></canvas></div>
</div>
<div class="section"><h2>Recent Trades</h2>
  <table id="trades-table"><thead><tr><th>Time</th><th>Strategy</th><th>Direction</th><th>P&L</th><th>Reason</th></tr></thead><tbody></tbody></table>
</div>
<div class="section"><h2>Signals</h2><div id="signals">(loading...)</div></div>
<div class="section"><h2>Go-Live Checklist</h2><div class="golive" id="golive">(loading...)</div></div>

<script>
const $ = s => document.querySelector(s);
const fmt = n => (n >= 0 ? '+' : '') + n.toLocaleString('en-IN', {maximumFractionDigits:0});
let eqChart = null;

async function fetchJSON(url) { try { const r = await fetch(url); return r.ok ? r.json() : null; } catch { return null; } }
async function fetchText(url) { try { const r = await fetch(url); return r.ok ? r.text() : ''; } catch { return ''; } }

function pnlClass(v) { return v >= 0 ? 'profit' : 'loss'; }

async function updateSummary() {
  const s = await fetchJSON('/api/summary');
  if (!s) return;
  $('#total-pnl').textContent = '₹' + fmt(s.total_pnl || 0);
  $('#total-pnl').className = 'value ' + pnlClass(s.total_pnl);
  $('#win-rate').textContent = (s.win_rate_pct || 0).toFixed(1) + '%';
  $('#total-trades').textContent = s.total_trades || 0;
  const tb = $('#strat-table tbody');
  tb.innerHTML = '';
  const stratStats = (s.strategy_stats || {});
  const keys = Object.keys(stratStats);
  const preferred = ['A','B','C','D','E'];
  let ordered = preferred.filter(k => keys.includes(k));
  if (ordered.length === 0) ordered = keys;
  for (const k of ordered) {
    const ss = stratStats[k] || {};
    const tr = document.createElement('tr');
    const pnl = ss.total_pnl || 0;
    tr.innerHTML = `<td>${k}</td><td>${ss.trades||0}</td><td class="${pnlClass(pnl)}">₹${fmt(pnl)}</td><td>${(ss.win_rate||0).toFixed(0)}%</td>`;
    tb.appendChild(tr);
  }
}

async function updateTrades() {
  const trades = await fetchJSON('/api/trades');
  if (!trades) return;
  const tb = $('#trades-table tbody');
  tb.innerHTML = '';
  for (const t of trades.slice(-20).reverse()) {
    const tr = document.createElement('tr');
    const pnl = parseFloat(t.net_pnl || 0);
    tr.innerHTML = `<td>${t.time_exit||''}</td><td>${t.strategy||''}</td><td>${t.direction||''}</td><td class="${pnlClass(pnl)}">₹${fmt(pnl)}</td><td>${t.exit_reason||''}</td>`;
    tb.appendChild(tr);
  }
  if (trades.length > 0) {
    let cumPnl = 0;
    const labels = [], data = [];
    for (const t of trades) {
      cumPnl += parseFloat(t.net_pnl || 0);
      labels.push(t.time_exit || '');
      data.push(cumPnl);
    }
    if (!eqChart) {
      eqChart = new Chart($('#equity-chart'), {
        type: 'line',
        data: { labels, datasets: [{ data, borderColor: '#2E4BCC', backgroundColor: 'rgba(46,75,204,0.1)', fill: true, tension: 0.3 }] },
        options: { plugins: { legend: { display: false } }, scales: { x: { display: false }, y: { ticks: { color: '#6b6b8a' }, grid: { color: '#2a2a40' } } } }
      });
    } else {
      eqChart.data.labels = labels;
      eqChart.data.datasets[0].data = data;
      eqChart.update();
    }
  }
}

async function updateSignals() {
  const txt = await fetchText('/api/signals');
  $('#signals').textContent = txt || '(no signals yet)';
}

async function updateGoLive() {
  const g = await fetchJSON('/api/golive');
  if (!g) return;
  const el = $('#golive');
  let html = `<div style="font-size:16px;font-weight:700;margin-bottom:8px;">${g.verdict} (${g.score}/${g.total})</div>`;
  for (const [k, v] of Object.entries(g.checks || {})) {
    html += `<div class="check"><span>${k}</span><span class="${v ? 'pass' : 'fail'}">${v ? '✓' : '✗'}</span></div>`;
  }
  el.innerHTML = html;
}

function tick() { $('#clock').textContent = new Date().toLocaleTimeString('en-IN'); }
setInterval(tick, 1000); tick();
setInterval(updateSummary, 5000); updateSummary();
setInterval(updateTrades, 30000); updateTrades();
setInterval(updateSignals, 10000); updateSignals();
setInterval(updateGoLive, 60000); updateGoLive();
</script>
</body>
</html>"""


class WebDashboard:
    """
    Flask web dashboard.  Start in a daemon thread via .run().
    Reads from data/ files — never computes P&L itself.
    """

    def __init__(
        self,
        pnl_engine: Any,
        trade_logger: Any,
        evaluator: Any,
    ) -> None:
        self.pnl = pnl_engine
        self.tl = trade_logger
        self.evaluator = evaluator
        self._app = None

    def _create_app(self):
        try:
            from flask import Flask, Response, jsonify
        except ImportError:
            logger.error("flask not installed — web dashboard unavailable")
            return None

        app = Flask(__name__)
        app.config["JSON_SORT_KEYS"] = False
        pnl = self.pnl
        evaluator = self.evaluator

        @app.route("/")
        def index():
            return Response(_HTML, content_type="text/html")

        @app.route("/api/summary")
        def api_summary():
            # Live P&L: read the continuously refreshed snapshot (realised + unrealised).
            path = os.path.join(settings.DATA_DIR, "pnl_snapshot.json")
            try:
                if os.path.exists(path):
                    with open(path) as f:
                        return jsonify(json.load(f))
            except Exception:
                pass
            return jsonify({})

        @app.route("/api/trades")
        def api_trades():
            path = os.path.join(settings.DATA_DIR, "paper_trades.csv")
            try:
                if os.path.exists(path):
                    with open(path) as f:
                        reader = csv.DictReader(f)
                        rows = list(reader)
                    return jsonify(rows[-50:])
            except Exception:
                pass
            return jsonify([])

        @app.route("/api/signals")
        def api_signals():
            path = os.path.join(settings.DATA_DIR, "paper_signals.log")
            try:
                if os.path.exists(path):
                    with open(path) as f:
                        lines = f.readlines()
                    return Response("".join(lines[-100:]), content_type="text/plain")
            except Exception:
                pass
            return Response("", content_type="text/plain")

        @app.route("/api/golive")
        def api_golive():
            try:
                summary = pnl.get_summary() if pnl else {}
                trades_path = os.path.join(settings.DATA_DIR, "paper_trades.csv")
                import pandas as pd

                if os.path.exists(trades_path):
                    df = pd.read_csv(trades_path)
                else:
                    df = pd.DataFrame()
                result = evaluator.evaluate(summary, df)
                return jsonify(result)
            except Exception:
                logger.exception("go-live evaluation error")
                return jsonify({"error": "evaluation failed"})

        return app

    def run(self, host: str = "127.0.0.1", port: int = 5050) -> None:
        # Default to loopback only — single-laptop deployment has no use for
        # LAN-exposed live PnL / signals. Caller must explicitly pass a public
        # host to override.
        self._app = self._create_app()
        if self._app is None:
            return
        self._app.run(host=host, port=port, debug=False, use_reloader=False)
