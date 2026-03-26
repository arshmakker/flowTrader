"""Pytest configuration.

This repo contains a mix of:
- fast, offline unit tests (should always run)
- legacy/manual/integration scripts under tests/ that require credentials,
  third-party modules, or live broker connectivity.

We explicitly ignore the latter so `pytest` is usable in CI/dev by default.
"""

# These are ignored at collection time (before imports execute).
collect_ignore = [
    "test_trade_logger.py",
    "test_smoke.py",
    "test_signal_engine.py",
    "test_risk_manager_ic.py",
    "test_risk_manager.py",
    "test_regime_filter.py",
    "test_all_fixes.py",
    "test_GetOption_Greek.py",
    "test_basket_order.py",
    "test_daily_price_series.py",
    "test_daily_target.py",
    "test_forgot_password.py",
    "test_integration.py",
    "test_multi_user_sessions.py",
    "test_multiple_sessions.py",
    "test_optionchain.py",
    "test_paper_trading.py",
    "test_place_order.py",
    "test_position_persistence.py",
    "test_product_convertion.py",
    "test_realtime_excel.py",
    "test_spancalc.py",
    "test_strategies.py",
    "test_tpseries.py",
    "test_watchlist.py",
    "test_websocket_feed.py",
]
