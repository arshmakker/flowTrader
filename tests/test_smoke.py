"""Smoke tests — verify all source files import without syntax errors."""

import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_main_compiles():
    import py_compile

    py_compile.compile(
        os.path.join(os.path.dirname(__file__), "..", "main.py"),
        doraise=True,
    )


def test_strategy_runner_compiles():
    import py_compile

    py_compile.compile(
        os.path.join(os.path.dirname(__file__), "..", "strategy_runner.py"),
        doraise=True,
    )


def test_api_helper_compiles():
    import py_compile

    py_compile.compile(
        os.path.join(os.path.dirname(__file__), "..", "api_helper.py"),
        doraise=True,
    )


def test_all_trading_system_modules_import():
    """Every module under trading_system/ should import without error."""
    base = os.path.join(os.path.dirname(__file__), "..", "trading_system")
    failures = []
    for root, _, files in os.walk(base):
        for f in files:
            if f.endswith(".py") and f != "__init__.py":
                rel = os.path.relpath(os.path.join(root, f), os.path.join(base, ".."))
                mod = rel.replace(os.sep, ".").removesuffix(".py")
                try:
                    importlib.import_module(mod)
                except Exception as e:
                    failures.append(f"{mod}: {e}")
    assert not failures, "Failed imports:\n" + "\n".join(failures)


def test_strategy_runner_imports():
    """strategy_runner.py should import without error."""
    import strategy_runner

    assert hasattr(strategy_runner, "is_market_hours")
    assert hasattr(strategy_runner, "get_weekly_expiry")
    assert hasattr(strategy_runner, "get_option_chain_data")
    assert hasattr(strategy_runner, "save_daily_metrics")
