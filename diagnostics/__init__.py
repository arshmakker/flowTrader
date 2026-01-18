"""
Diagnostics Module

Provides synthetic data generation and regime validation tests.
"""

from .synthetic_data import SyntheticMarketGenerator
from .synthetic_regime_tests import run_synthetic_regime_tests

__all__ = ['SyntheticMarketGenerator', 'run_synthetic_regime_tests']
